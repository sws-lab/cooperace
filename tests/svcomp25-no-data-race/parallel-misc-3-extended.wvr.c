// This file is part of the SV-Benchmarks collection of verification tasks:
// https://gitlab.com/sosy-lab/benchmarking/sv-benchmarks
//
// SPDX-FileCopyrightText: 2021 F. Schuessele <schuessf@informatik.uni-freiburg.de>
// SPDX-FileCopyrightText: 2021 D. Klumpp <klumpp@informatik.uni-freiburg.de>
//
// SPDX-License-Identifier: LicenseRef-BSD-3-Clause-Attribution-Vandikas

typedef unsigned long int pthread_t;

union pthread_attr_t
{
  char __size[36];
  long int __align;
};
typedef union pthread_attr_t pthread_attr_t;

extern void __assert_fail(const char *__assertion, const char *__file,
      unsigned int __line, const char *__function)
     __attribute__ ((__nothrow__ , __leaf__)) __attribute__ ((__noreturn__));
void reach_error() { __assert_fail("0", "parallel-misc-3-extended.wvr.c", 21, __extension__ __PRETTY_FUNCTION__); }
extern int pthread_create (pthread_t *__restrict __newthread,
      const pthread_attr_t *__restrict __attr,
      void *(*__start_routine) (void *),
      void *__restrict __arg) __attribute__ ((__nothrow__)) __attribute__ ((__nonnull__ (1, 3)));
extern int pthread_join (pthread_t __th, void **__thread_return);

extern int   __VERIFIER_nondet_int(void);
extern _Bool __VERIFIER_nondet_bool(void);

extern void abort(void);
void assume_abort_if_not(int cond) {
  if(!cond) {abort();}
}

_Atomic int pos;
_Bool d1, d2, d3, g1, g2, g3;

void* thread1(void* _argptr) {
  while (g1) {
    if (d1) {
      pos++;
    } else {
      pos--;
    }
    d1 = !d1;
    if (d1) {
      if (__VERIFIER_nondet_bool()) {
        g1 = 0;
      }
    }
  }

  return 0;
}

void* thread2(void* _argptr) {
  while (g2) {
    if (d2) {
      pos += 2;
    } else {
      pos -= 2;
    }
    d2 = !d2;
    if (d2) {
      if (__VERIFIER_nondet_bool()) {
        g2 = 0;
      }
    }
  }

  return 0;
}

void* thread3(void* _argptr) {
  while (g3) {
    if (d3) {
      pos += 2;
    } else {
      pos -= 2;
    }
    d3 = !d3;
    if (d3) {
      if (__VERIFIER_nondet_bool()) {
        g3 = 0;
      }
    }
  }

  return 0;
}

int main() {
  pthread_t t1, t2, t3;

  // initialize global variables
  d1 = 1;
  d2 = 1;
  d3 = 1;
  g1 = 1;
  g2 = 1;
  g3 = 1;

  // main method
  pthread_create(&t1, 0, thread1, 0);
  pthread_create(&t2, 0, thread2, 0);
  pthread_create(&t3, 0, thread3, 0);
  pthread_join(t1, 0);
  pthread_join(t2, 0);
  pthread_join(t3, 0);

  assume_abort_if_not(pos != 0);
  reach_error();

  return 0;
}
